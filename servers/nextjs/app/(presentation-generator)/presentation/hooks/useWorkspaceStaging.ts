"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
    if (networkError && !pollingPaused) return { title: "작업 상태를 확인하는 중이에요", description: "연결이 잠시 불안정합니다. 작업은 서버에서 계속 진행될 수 있습니다.", notice: "자동으로 다시 확인합니다." };
    if (!operation) return { title: "작업 상태를 확인하는 중이에요", description: "디자인 변경 작업을 찾고 있습니다." };
    if (pollingPaused && operation.status === "queued") return { title: "작업 시작 대기를 계속 확인할 수 있어요", description: operation.capacityWaiting ? "자동 상태 확인을 잠시 멈췄습니다. 마지막 확인 시 생성 용량 대기 중이었습니다." : "자동 상태 확인을 잠시 멈췄습니다. 마지막 확인 시 작업 시작 대기 중이었습니다.", notice: "상태 새로 고침으로 다시 확인하거나 작업 대화에서 가능한 조치를 선택해 주세요." };
    if (terminal) return { title: operation.status === "cancelled" ? "디자인 변경이 취소되었습니다" : operation.status === "deadline_exceeded" || operation.status === "expired" ? "디자인 변경 시간이 만료되었습니다" : "디자인 변경을 완료하지 못했습니다", description: operation.error || "작업 대화에서 원인을 확인하고 가능한 작업을 선택해 주세요.", notice: "작업 대화에서 다시 시도 또는 취소 상태를 확인할 수 있습니다." };
    if (operation.status === "queued") return operation.capacityWaiting ? { title: "생성 용량을 기다리고 있어요", description: "사용 가능한 생성 슬롯이 생기면 자동으로 시작합니다.", notice: operation.queuePosition === undefined ? undefined : `대기 순번 ${operation.queuePosition}번째` } : { title: "디자인 변경을 대기 중이에요", description: "작업이 아직 시작되지 않았습니다. 시작되면 자동으로 진행 상황을 표시합니다.", notice: "60초 이상 대기해도 작업은 자동으로 실패하지 않습니다." };
    if (["waiting_for_assets", "blocked", "approval_required", "awaiting_approval"].includes(operation.status)) return { title: "작업에 확인이 필요해요", description: "필요한 파일 또는 승인 상태를 작업 대화에서 확인해 주세요.", notice: "작업 대화에서 가능한 조치를 선택할 수 있습니다." };
    if (operation.status === "cancelling") return { title: "디자인 변경을 취소하는 중이에요", description: "취소가 확인될 때까지 잠시 기다려 주세요." };
    if (operation.status === "succeeded") return { title: "디자인 변경을 마무리하고 있어요", description: "완료된 슬라이드를 불러오는 중입니다." };
    return { title: "디자인을 적용하고 있어요", description: "원본 자료를 유지하며 새 발표자료를 만들고 있습니다." };
  }, [networkError, operation, pollingPaused, terminal]);

  return { content, pollingPaused, refresh, terminal };
}
