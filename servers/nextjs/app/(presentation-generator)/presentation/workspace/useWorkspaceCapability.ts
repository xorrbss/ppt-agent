"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AUTH_SESSION_EXPIRED_EVENT } from "../../services/api/api-error-handler";
import { getWorkspaceCapabilities } from "../../services/api/workspace";
import type { WorkspaceCapability } from "./types";

export type WorkspaceCapabilityState =
  "disabled" | "checking" | "supported" | "unavailable";
const EMPTY_CAPABILITY: WorkspaceCapability = {
  supported: false,
  timeline: false,
  commands: false,
  events: false,
};

export function useWorkspaceCapability(
  enabled: boolean,
  presentationId?: string,
): {
  state: WorkspaceCapabilityState;
  capability: WorkspaceCapability;
  refresh: () => void;
} {
  const [snapshot, setSnapshot] = useState<{
    presentationId?: string;
    state: WorkspaceCapabilityState;
    capability: WorkspaceCapability;
  }>({
    presentationId,
    state: enabled ? "checking" : "disabled",
    capability: EMPTY_CAPABILITY,
  });
  const revalidateRef = useRef<(() => void) | null>(null);
  const refresh = useCallback(() => revalidateRef.current?.(), []);

  useEffect(() => {
    setSnapshot({
      presentationId,
      state: enabled ? "checking" : "disabled",
      capability: EMPTY_CAPABILITY,
    });
    if (!enabled) {
      return;
    }
    let disposed = false;
    let controller: AbortController | null = null;
    let channel: BroadcastChannel | null = null;
    let lastPublishedSignature: string | undefined;

    try {
      if (typeof BroadcastChannel !== "undefined") {
        channel = new BroadcastChannel("presenton-chat-first-capability-v1");
      }
    } catch {
      // Focus and page visibility still revalidate in restricted browsers.
    }

    const revalidate = (publish: boolean) => {
      if (disposed) return;
      controller?.abort();
      const request = new AbortController();
      controller = request;
      // Keep the existing chat mounted while checking the server again.
      const commit = (next: WorkspaceCapability) => {
        if (disposed || request.signal.aborted || controller !== request) return;
        setSnapshot({
          presentationId,
          state: next.supported && next.timeline ? "supported" : "unavailable",
          capability: next,
        });
        const signature = JSON.stringify(next);
        const changed = lastPublishedSignature !== signature;
        lastPublishedSignature = signature;
        // A response to another tab's invalidation never echoes it back.
        if (publish && changed) {
          try {
            channel?.postMessage({
              kind: "capability",
              key: presentationId,
              snapshot: { capability: next },
            });
          } catch {
            // Local authoritative results do not depend on channel delivery.
          }
        }
      };
      void getWorkspaceCapabilities(request.signal, presentationId).then(
        commit,
        () => commit(EMPTY_CAPABILITY),
      );
    };
    const refreshLocal = () => revalidate(true);
    const onAuthEnded = () => {
      controller?.abort();
      controller = null;
      setSnapshot({
        presentationId,
        state: "unavailable",
        capability: EMPTY_CAPABILITY,
      });
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") refreshLocal();
    };
    if (channel) {
      channel.onmessage = (event: MessageEvent) => {
        const message = event.data;
        if (
          !message ||
          message.kind !== "capability" ||
          message.key !== presentationId
        ) return;
        const next = message.snapshot?.capability;
        // Cross-tab snapshots are signals, never authority to grant access.
        // A restrictive signal also fences any older in-flight grant.
        if (
          next?.commands === false ||
          next?.supported === false ||
          next?.timeline === false
        ) {
          setSnapshot((current) => ({
            ...current,
            capability: { ...current.capability, commands: false },
          }));
        }
        revalidate(false);
      };
    }
    revalidateRef.current = refreshLocal;
    window.addEventListener("focus", refreshLocal);
    window.addEventListener("pageshow", refreshLocal);
    window.addEventListener(AUTH_SESSION_EXPIRED_EVENT, onAuthEnded);
    window.addEventListener("presenton:auth-logout", onAuthEnded);
    document.addEventListener("visibilitychange", onVisibility);
    refreshLocal();
    return () => {
      disposed = true;
      controller?.abort();
      revalidateRef.current = null;
      window.removeEventListener("focus", refreshLocal);
      window.removeEventListener("pageshow", refreshLocal);
      window.removeEventListener(AUTH_SESSION_EXPIRED_EVENT, onAuthEnded);
      window.removeEventListener("presenton:auth-logout", onAuthEnded);
      document.removeEventListener("visibilitychange", onVisibility);
      channel?.close();
    };
  }, [enabled, presentationId]);

  if (!enabled) return { state: "disabled", capability: EMPTY_CAPABILITY, refresh };
  if (snapshot.presentationId !== presentationId || snapshot.state === "disabled") {
    return { state: "checking", capability: EMPTY_CAPABILITY, refresh };
  }
  return { state: snapshot.state, capability: snapshot.capability, refresh };
}
