"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { getWorkspaceTimeline } from "../../services/api/workspace";
import type {
  WorkspaceOperationAnchor,
  WorkspaceTimelineAnchor,
} from "../workspace/types";

const NORMAL_POLL_INTERVAL_MS = 2_000;
const QUEUED_OBSERVATION_MS = 5 * 60_000;
const TERMINAL_STATES = new Set([
  "failed",
  "dead_letter",
  "cancelled",
  "rejected",
  "expired",
  "deadline_exceeded",
]);

type PresentationData = { slides?: unknown[] } | undefined;
type FetchSlides = (options?: {
  clearHistory?: boolean;
  quiet?: boolean;
  signal?: AbortSignal;
}) => Promise<PresentationData>;

interface RouterLike {
  replace(url: string): void;
}

export interface WorkspaceStagingContent {
  title: string;
  description: string;
  notice?: string;
}

export function useWorkspaceStaging({
  workspaceStaging,
  presentationId,
  slidesLength,
  pathname,
  router,
  fetchUserSlides,
}: {
  workspaceStaging: boolean;
  presentationId: string;
  slidesLength: number;
  pathname: string;
  router: RouterLike;
  fetchUserSlides: FetchSlides;
}) {
  const t = useTranslations("presentationStaging");
  const [operation, setOperation] = useState<WorkspaceOperationAnchor | null>(null);
  const [networkError, setNetworkError] = useState(false);
  const [pollingPaused, setPollingPaused] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);
  const [manualWindowEndsAt, setManualWindowEndsAt] = useState<number | null>(null);
  const operationRef = useRef<WorkspaceOperationAnchor | null>(null);
  const operationIdRef = useRef<string | null>(null);
  const queuedAtRef = useRef<number | null>(null);
  const observedOperationRef = useRef<{ operationId: string; status: string } | null>(null);

  useEffect(() => {
    if (!workspaceStaging || slidesLength === 0) return;
    setPollingPaused(false);
    setNetworkError(false);
    const params = new URLSearchParams(window.location.search);
    if (!params.has("workspace_staging")) return;
    params.delete("workspace_staging");
    const query = params.toString();
    router.replace(`${pathname}${query ? `?${query}` : ""}`);
  }, [pathname, router, slidesLength, workspaceStaging]);

  useEffect(() => {
    if (!workspaceStaging || slidesLength > 0) return;
    let cancelled = false;
    let timer: number | null = null;
    let activeController: AbortController | null = null;
    let networkFailures = 0;
    const observationStartedAt = Date.now();
    setPollingPaused(false);

    const findOperation = (items: WorkspaceTimelineAnchor[]) => {
      const candidates = items.filter(
        (item): item is WorkspaceOperationAnchor =>
          item.anchorType === "operation" &&
          (item.operationType === "design.apply" || item.operationType === "template.apply"),
      );
      const knownId = operationIdRef.current;
      if (knownId) {
        const known = candidates.find((item) => item.operationId === knownId);
        if (known && !TERMINAL_STATES.has(known.status)) return known;
        if (operationRef.current && TERMINAL_STATES.has(operationRef.current.status)) {
          const retry = candidates.find(
            (item) =>
              item.operationId !== knownId &&
              (item.retryOfOperationId === knownId ||
                (known !== undefined && item.position > known.position)),
          );
          if (retry) return retry;
          if (known) return known;
          return candidates.reduce<WorkspaceOperationAnchor | undefined>(
            (latest, item) => latest === undefined || item.position > latest.position ? item : latest,
            undefined,
          );
        }
        return undefined;
      }
      return candidates.reduce<WorkspaceOperationAnchor | undefined>(
        (latest, item) => latest === undefined || item.position > latest.position ? item : latest,
        undefined,
      );
    };

    const schedule = () => {
      if (cancelled) return;
      const current = operationRef.current;
      const queuedFor = queuedAtRef.current === null ? 0 : Date.now() - queuedAtRef.current;
      if (
        current?.status === "queued" &&
        (manualWindowEndsAt !== null
          ? Date.now() >= manualWindowEndsAt
          : queuedFor >= QUEUED_OBSERVATION_MS)
      ) {
        setPollingPaused(true);
        return;
      }
      const queuedInterval = current?.status === "queued" && queuedFor >= 60_000
        ? Math.min(15_000, 5_000 + Math.floor((queuedFor - 60_000) / 60_000) * 5_000)
        : NORMAL_POLL_INTERVAL_MS;
      const interval = networkFailures > 0
        ? Math.min(30_000, NORMAL_POLL_INTERVAL_MS * 2 ** networkFailures)
        : queuedInterval;
      timer = window.setTimeout(poll, interval);
    };

    const poll = async () => {
      const controller = new AbortController();
      activeController = controller;
      const timeout = window.setTimeout(() => controller.abort(), 30_000);
      try {
        const latest = await getWorkspaceTimeline(presentationId, { limit: 8, signal: controller.signal });
        if (cancelled) return;
        let next = findOperation(latest.items);
        if (!next) {
          const expanded = await getWorkspaceTimeline(presentationId, { limit: 50, signal: controller.signal });
          if (cancelled) return;
          next = findOperation(expanded.items);
        }
        if (next) {
          const previous = observedOperationRef.current;
          operationIdRef.current = next.operationId;
          operationRef.current = next;
          setOperation(next);
          setNetworkError(false);
          networkFailures = 0;
          if (next.status === "queued") {
            if (previous?.operationId !== next.operationId || previous.status !== "queued") {
              const createdAt = previous === null && next.createdAt ? Date.parse(next.createdAt) : Number.NaN;
              queuedAtRef.current = Number.isNaN(createdAt) ? Date.now() : createdAt;
            }
          } else {
            queuedAtRef.current = null;
            setPollingPaused(false);
            setManualWindowEndsAt(null);
          }
          observedOperationRef.current = { operationId: next.operationId, status: next.status };
        }
        const refreshed = await Promise.race([
          fetchUserSlides({ clearHistory: false, quiet: true, signal: controller.signal }),
          new Promise<undefined>((resolve) => controller.signal.addEventListener("abort", () => resolve(undefined), { once: true })),
        ]);
        if (!refreshed) {
          networkFailures += 1;
          setNetworkError(true);
        }
        if (cancelled) return;
        if ((refreshed?.slides?.length ?? 0) > 0) {
          const params = new URLSearchParams(window.location.search);
          params.delete("workspace_staging");
          const query = params.toString();
          router.replace(`${pathname}${query ? `?${query}` : ""}`);
          return;
        }
      } catch {
        if (cancelled) return;
        networkFailures += 1;
        setNetworkError(true);
      } finally {
        window.clearTimeout(timeout);
        if (activeController === controller) activeController = null;
      }
      schedule();
    };

    timer = window.setTimeout(poll, 500);
    return () => {
      cancelled = true;
      activeController?.abort();
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [fetchUserSlides, manualWindowEndsAt, pathname, presentationId, refreshToken, router, slidesLength, workspaceStaging]);

  const refresh = useCallback(() => {
    setNetworkError(false);
    setPollingPaused(false);
    setManualWindowEndsAt(Date.now() + 30_000);
    setRefreshToken((value) => value + 1);
  }, []);

  const terminal = operation !== null && TERMINAL_STATES.has(operation.status);
  const content = useMemo<WorkspaceStagingContent>(() => {
    if (networkError && !pollingPaused) return { title: t("checkingStatus"), description: t("connectionUnstable"), notice: t("retryAutomatically") };
    if (!operation) return { title: t("checkingStatus"), description: t("findingOperation") };
    if (pollingPaused && operation.status === "queued") return { title: t("canContinueChecking"), description: operation.capacityWaiting ? t("capacityCheckPaused") : t("queueCheckPaused"), notice: t("refreshOrChooseAction") };
    if (terminal) return { title: operation.status === "cancelled" ? t("cancelled") : operation.status === "deadline_exceeded" || operation.status === "expired" ? t("expired") : t("failed"), description: operation.error || t("inspectCause"), notice: t("retryOrCancel") };
    if (operation.status === "queued") return operation.capacityWaiting ? { title: t("waitingForCapacity"), description: t("startsWhenAvailable"), notice: operation.queuePosition === undefined ? undefined : t("queuePosition", { position: operation.queuePosition }) } : { title: t("waitingForDesign"), description: t("notStartedYet"), notice: t("waitDoesNotFail") };
    if (["waiting_for_assets", "blocked", "approval_required", "awaiting_approval"].includes(operation.status)) return { title: t("needsAttention"), description: t("checkFilesOrApproval"), notice: t("chooseAction") };
    if (operation.status === "cancelling") return { title: t("cancelling"), description: t("waitForCancellation") };
    if (operation.status === "succeeded") return { title: t("finishing"), description: t("loadingSlides") };
    return { title: t("applyingDesign"), description: t("creatingFromSource") };
  }, [networkError, operation, pollingPaused, terminal, t]);

  return { content, pollingPaused, refresh, terminal };
}
