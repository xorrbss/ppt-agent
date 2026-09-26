"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

import {
  createPublicV2OperationClient,
  shouldPollPublicV2Operation,
  type PublicV2ExportRequest,
  type PublicV2GenerationRequest,
  type PublicV2OperationClient,
  type PublicV2OperationClientOptions,
  type PublicV2OperationSnapshot,
} from "./operation-client.ts";

export type UsePublicV2OperationOptions = PublicV2OperationClientOptions & {
  /** Set false only when the caller needs to control the first resume itself. */
  autoResume?: boolean;
  /** Visible-tab polling cadence, clamped to 1-10 seconds. */
  pollIntervalMs?: number;
  /** Background-tab cadence, clamped to 10-60 seconds. */
  hiddenPollIntervalMs?: number;
};

function boundedInterval(value: number | undefined, fallback: number, min: number, max: number): number {
  if (value === undefined || !Number.isFinite(value)) return fallback;
  return Math.max(min, Math.min(max, Math.trunc(value)));
}

/**
 * Thin React adapter over the durable public-v2 operation client. Components
 * receive the same snapshot and form methods without owning polling/cursors.
 */
export function usePublicV2Operation(options: UsePublicV2OperationOptions = {}): {
  client: PublicV2OperationClient;
  snapshot: PublicV2OperationSnapshot;
  submitGeneration: (request: PublicV2GenerationRequest) => ReturnType<PublicV2OperationClient["submitGeneration"]>;
  submitExport: (presentationId: string, request: PublicV2ExportRequest) => ReturnType<PublicV2OperationClient["submitExport"]>;
  recoverPendingExport: () => ReturnType<PublicV2OperationClient["recoverPendingExport"]>;
  refresh: () => ReturnType<PublicV2OperationClient["refresh"]>;
  cancel: () => ReturnType<PublicV2OperationClient["cancel"]>;
  clear: () => void;
} {
  const [client] = useState(() => createPublicV2OperationClient(options));
  const snapshot = useSyncExternalStore(
    (listener) => client.subscribe(listener),
    () => client.snapshot,
    () => client.snapshot,
  );

  useEffect(() => {
    if (options.autoResume === false) return;
    void client.resume().catch(() => {
      // The snapshot contains the normalized retryable failure for the UI.
    });
  }, [client, options.autoResume]);

  const shouldPoll = shouldPollPublicV2Operation(snapshot);

  useEffect(() => {
    if (!shouldPoll || typeof document === "undefined") return;
    const visibleInterval = boundedInterval(options.pollIntervalMs, 2_000, 1_000, 10_000);
    const hiddenInterval = boundedInterval(options.hiddenPollIntervalMs, 15_000, 10_000, 60_000);
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let inFlight = false;
    let consecutiveFailures = 0;

    const clearTimer = () => {
      if (timer !== null) clearTimeout(timer);
      timer = null;
    };
    const schedule = (delay: number) => {
      clearTimer();
      if (!disposed) timer = setTimeout(() => void tick(), delay);
    };
    const tick = async () => {
      if (disposed || inFlight) return;
      const current = client.snapshot;
      if (!shouldPollPublicV2Operation(current)) return;
      inFlight = true;
      try {
        await client.refresh();
        consecutiveFailures = 0;
      } catch {
        consecutiveFailures += 1;
      } finally {
        inFlight = false;
      }
      const latest = client.snapshot;
      if (disposed || !shouldPollPublicV2Operation(latest)) return;
      const retryBackoff = Math.min(
        visibleInterval * (2 ** Math.min(consecutiveFailures, 4)),
        30_000,
      );
      schedule(document.visibilityState === "hidden" ? hiddenInterval : retryBackoff);
    };
    const onVisibilityChange = () => {
      if (document.visibilityState !== "visible") {
        schedule(hiddenInterval);
        return;
      }
      clearTimer();
      void tick();
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    schedule(document.visibilityState === "hidden" ? hiddenInterval : visibleInterval);
    return () => {
      disposed = true;
      clearTimer();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [
    client,
    options.hiddenPollIntervalMs,
    options.pollIntervalMs,
    shouldPoll,
    snapshot.operationId,
  ]);

  return {
    client,
    snapshot,
    submitGeneration: client.submitGeneration.bind(client),
    submitExport: client.submitExport.bind(client),
    recoverPendingExport: client.recoverPendingExport.bind(client),
    refresh: client.refresh.bind(client),
    cancel: client.cancel.bind(client),
    clear: client.clear.bind(client),
  };
}
