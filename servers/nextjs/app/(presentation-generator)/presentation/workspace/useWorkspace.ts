"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  getWorkspaceCapabilities,
  getWorkspaceEvents,
  getWorkspaceTimeline,
  postWorkspaceCommand,
  postWorkspaceOperationAction,
  streamWorkspaceEvents,
  WorkspaceApiError,
} from "../../services/api/workspace";
import {
  applyOperationEvents,
  createWorkspaceProjection,
  mergeTimelinePage,
} from "./timelineProjection";
import { workspacePresentationChangeFor } from "./workspaceCompletion";
import { WorkspaceReconnectBackoff } from "./workspaceStream";
import type {
  WorkspaceCapability,
  WorkspaceCommandOptions,
  WorkspaceCommandRequest,
  WorkspaceOperationActionRequest,
  WorkspaceOperationAnchor,
  WorkspacePresentationChange,
  WorkspacePresentationChangedHandler,
  WorkspacePresentationRefreshState,
  WorkspaceProjection,
} from "./types";

export type WorkspaceCapabilityState =
  "disabled" | "checking" | "supported" | "unavailable";
export type WorkspaceConnectionState =
  "connecting" | "live" | "polling" | "offline";
export type WorkspaceRetryReason = "ambiguous" | "revision_conflict";

type WorkspacePendingIntent =
  | { kind: "command"; request: WorkspaceCommandRequest }
  | {
      kind: "action";
      operationId: string;
      request: WorkspaceOperationActionRequest;
    };

export interface WorkspaceRetryIntent {
  reason: WorkspaceRetryReason;
  command?: string;
  action?: WorkspaceOperationActionRequest["action"];
}

/** States an operation cannot leave, so its committed result is final. */
const TERMINAL_OPERATION_STATUSES = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "dead_letter",
]);

function isTerminalWorkspaceEvent(event: {
  type?: string;
  status?: string;
}): boolean {
  return (
    event.type === "operation.result" ||
    (event.status !== undefined &&
      TERMINAL_OPERATION_STATUSES.has(event.status))
  );
}

const EMPTY_CAPABILITY: WorkspaceCapability = {
  supported: false,
  timeline: false,
  commands: false,
  events: false,
};

function wait(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(resolve, milliseconds);
    signal.addEventListener(
      "abort",
      () => {
        window.clearTimeout(timeout);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

export function useWorkspaceCapability(
  enabled: boolean,
  presentationId?: string,
): {
  state: WorkspaceCapabilityState;
  capability: WorkspaceCapability;
  refresh: () => void;
} {
  const [state, setState] = useState<WorkspaceCapabilityState>(
    enabled ? "checking" : "disabled",
  );
  const [capability, setCapability] = useState(EMPTY_CAPABILITY);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    if (!enabled) {
      setState("disabled");
      setCapability(EMPTY_CAPABILITY);
      return;
    }
    const controller = new AbortController();
    setState("checking");
    getWorkspaceCapabilities(controller.signal, presentationId)
      .then((next) => {
        if (controller.signal.aborted) return;
        setCapability(next);
        setState(next.supported && next.timeline ? "supported" : "unavailable");
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        console.warn(
          "Workspace capability check failed; writes remain disabled.",
          error,
        );
        setCapability(EMPTY_CAPABILITY);
        setState("unavailable");
      });
    return () => controller.abort();
  }, [enabled, presentationId, reloadToken]);

  const refresh = useCallback(() => setReloadToken((token) => token + 1), []);

  return { state, capability, refresh };
}

function idempotencyKey(): string {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function isRevisionConflict(error: unknown): error is WorkspaceApiError {
  return (
    error instanceof WorkspaceApiError &&
    (error.code === "revision_conflict" ||
      (error.status === 409 &&
        (error.code === "stale_revision" ||
          error.code === "stale_presentation_revision")))
  );
}

function publicWorkspaceErrorMessage(error: unknown, fallback: string): string {
  return error instanceof WorkspaceApiError && error.message.trim().length > 0
    ? error.message
    : fallback;
}

function isAmbiguousWriteFailure(error: unknown): boolean {
  if (error instanceof WorkspaceApiError && error.retryable === false)
    return false;
  return (
    !(error instanceof WorkspaceApiError) ||
    error.status === 0 ||
    error.status === 408 ||
    error.status === 429 ||
    error.status >= 500
  );
}

interface UseWorkspaceTimelineOptions {
  presentationId: string;
  enabled: boolean;
  commandsEnabled?: boolean;
  onPresentationChanged?: WorkspacePresentationChangedHandler;
}


export function useWorkspaceTimeline({
  presentationId,
  enabled,
  commandsEnabled = true,
  onPresentationChanged,
}: UseWorkspaceTimelineOptions) {
  const [projection, setProjection] = useState<WorkspaceProjection>(
    createWorkspaceProjection,
  );
  const [loading, setLoading] = useState(enabled);
  const [submitting, setSubmitting] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [retryIntent, setRetryIntent] = useState<WorkspaceRetryIntent | null>(
    null,
  );
  const [connection, setConnection] =
    useState<WorkspaceConnectionState>("connecting");
  const [presentationRefreshStates, setPresentationRefreshStates] = useState<
    Record<string, WorkspacePresentationRefreshState>
  >({});
  const projectionRef = useRef<WorkspaceProjection>(
    createWorkspaceProjection(),
  );
  const cursorRef = useRef<string | undefined>(undefined);
  const completionSeededRef = useRef(false);
  const succeededRef = useRef(new Set<string>());
  const pendingIntentRef = useRef<WorkspacePendingIntent | null>(null);

  const commitProjection = useCallback(
    (
      update: (current: WorkspaceProjection) => WorkspaceProjection,
    ): WorkspaceProjection => {
      const next = update(projectionRef.current);
      projectionRef.current = next;
      cursorRef.current = next.eventHighWatermark;
      setProjection(next);
      return next;
    },
    [],
  );

  const rebase = useCallback(
    async (signal?: AbortSignal) => {
      const page = await getWorkspaceTimeline(presentationId, { signal });
      if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
      commitProjection(() =>
        mergeTimelinePage(createWorkspaceProjection(), page),
      );
      return page;
    },
    [commitProjection, presentationId],
  );

  useEffect(() => {
    if (!enabled || !presentationId) return;
    const controller = new AbortController();
    setLoading(true);
    setHistoryError(null);
    setRequestError(null);
    setConnection("connecting");
    commitProjection(() => createWorkspaceProjection());
    completionSeededRef.current = false;
    succeededRef.current = new Set();
    pendingIntentRef.current = null;
    setRetryIntent(null);
    setPresentationRefreshStates({});

    rebase(controller.signal)
      .then(() => setConnection("live"))
      .catch((loadError: unknown) => {
        if (controller.signal.aborted) return;
        setHistoryError(
          publicWorkspaceErrorMessage(
            loadError,
            "대화 기록을 불러오지 못했습니다.",
          ),
        );
        setConnection("offline");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [commitProjection, enabled, presentationId, rebase]);

  useEffect(() => {
    if (!enabled || loading || historyError || !presentationId) return;
    const controller = new AbortController();
    // Count disconnected connection attempts, not empty-event attempts. A proxy
    // can deliver one valid event and then close immediately; resetting here on
    // every event would pin that loop to the first backoff interval forever.
    const reconnectBackoff = new WorkspaceReconnectBackoff();
    let projectionRecovery: Promise<void> | null = null;

    const recoverProjection = (): Promise<void> => {
      if (projectionRecovery) return projectionRecovery;
      const recovery = (async () => {
        // An operation event can become visible just before its top-level anchor.
        // Keep the event cursor unchanged until a timeline snapshot supplies that
        // anchor, then catch up once more from the committed projection cursor.
        for (let attempt = 0; attempt < 3; attempt += 1) {
          const timeline = await getWorkspaceTimeline(presentationId, {
            signal: controller.signal,
          });
          if (controller.signal.aborted)
            throw new DOMException("Aborted", "AbortError");
          const refreshed = commitProjection((current) =>
            mergeTimelinePage(current, timeline, { preservePagination: true }),
          );
          if (!refreshed.rebaseRequired) {
            const backfill = await getWorkspaceEvents(presentationId, {
              after: refreshed.eventHighWatermark,
              signal: controller.signal,
            });
            const caughtUp = commitProjection((current) =>
              applyOperationEvents(current, backfill),
            );
            if (!caughtUp.rebaseRequired) return;
          }
          if (attempt < 2) await wait(250, controller.signal);
        }
        throw new WorkspaceApiError(
          "대화 작업의 최신 상태를 복구하지 못했습니다.",
          409,
          "projection_recovery_failed",
          false,
        );
      })();
      const trackedRecovery = recovery.finally(() => {
        if (projectionRecovery === trackedRecovery) projectionRecovery = null;
      });
      projectionRecovery = trackedRecovery;
      return trackedRecovery;
    };

    const applyEventPage = (
      page: Awaited<ReturnType<typeof getWorkspaceEvents>>,
    ) => {
      if (page.cursorSafe === false) {
        throw new WorkspaceApiError(
          "이벤트 응답 형식을 확인할 수 없어 다시 연결합니다.",
          503,
          "stream_protocol_error",
          true,
        );
      }
      const next = commitProjection((current) =>
        applyOperationEvents(current, page),
      );
      if (next.rebaseRequired) return recoverProjection();
      // A terminal event reports the outcome but deliberately carries no result
      // payload: raw provider output is excluded from the durable event log.
      // Pull the committed result from the timeline so a finished operation
      // shows its answer without waiting for a reload.
      if (page.events.some(isTerminalWorkspaceEvent)) return recoverProjection();
      return Promise.resolve();
    };

    const applyPage = async () => {
      const page = await getWorkspaceEvents(presentationId, {
        after: cursorRef.current,
        signal: controller.signal,
      });
      await applyEventPage(page);
    };

    const run = async () => {
      while (!controller.signal.aborted) {
        try {
          await applyPage();
          setConnection("live");
          await streamWorkspaceEvents(presentationId, {
            after: cursorRef.current,
            signal: controller.signal,
            onEvent: async (event, cursor) => {
              await applyEventPage({
                events: [event],
                eventHighWatermark: cursor,
                cursorSafe: true,
              });
            },
          });
          // A clean EOF is still a disconnect. Back off before reconnecting so
          // a proxy that closes immediately cannot create a hot loop.
          const reconnect = reconnectBackoff.recordDisconnect();
          setConnection(
            reconnect.disconnectCount >= 2 ? "polling" : "connecting",
          );
          await wait(reconnect.delayMs, controller.signal);
        } catch (streamError: unknown) {
          if (controller.signal.aborted) return;
          if (
            streamError instanceof WorkspaceApiError &&
            (streamError.status === 410 ||
              streamError.code === "cursor_expired" ||
              streamError.code === "event_cursor_expired")
          ) {
            try {
              await rebase(controller.signal);
              reconnectBackoff.resetAfterRebase();
              continue;
            } catch (rebaseError: unknown) {
              if (controller.signal.aborted) return;
              setHistoryError(
                publicWorkspaceErrorMessage(
                  rebaseError,
                  "대화 기록 복구에 실패했습니다.",
                ),
              );
              setConnection("offline");
              return;
            }
          }
          if (
            streamError instanceof WorkspaceApiError &&
            streamError.retryable === false
          ) {
            setHistoryError(streamError.message);
            setConnection("offline");
            return;
          }
          const reconnect = reconnectBackoff.recordDisconnect();
          setConnection(
            reconnect.disconnectCount >= 2 ? "polling" : "connecting",
          );
          try {
            await wait(reconnect.delayMs, controller.signal);
          } catch {
            return;
          }
        }
      }
    };
    void run();
    return () => controller.abort();
  }, [
    commitProjection,
    enabled,
    historyError,
    loading,
    presentationId,
    rebase,
  ]);

  useEffect(() => {
    const changes = projection.items
      .filter(
        (item): item is WorkspaceOperationAnchor => item.anchorType === "operation",
      )
      .map(workspacePresentationChangeFor)
      .filter((change): change is WorkspacePresentationChange => Boolean(change));
    if (!completionSeededRef.current) {
      succeededRef.current = new Set(changes.map((change) => change.operationId));
      completionSeededRef.current = true;
      return;
    }
    const newChanges = changes.filter(
      (change) => !succeededRef.current.has(change.operationId),
    );
    newChanges.forEach((change) => succeededRef.current.add(change.operationId));
    for (const change of newChanges) {
      if (!onPresentationChanged) continue;
      setPresentationRefreshStates((current) => ({
        ...current,
        [change.operationId]: "refreshing",
      }));
      void Promise.resolve(onPresentationChanged(change))
        .then(() => {
          setPresentationRefreshStates((current) => ({
            ...current,
            [change.operationId]: "refreshed",
          }));
        })
        .catch((error) => {
          setPresentationRefreshStates((current) => ({
            ...current,
            [change.operationId]: "failed",
          }));
          console.warn(
            "Unable to refresh the presentation after workspace completion.",
            error,
          );
        });
    }
  }, [onPresentationChanged, projection.items]);

  const loadOlder = useCallback(async () => {
    if (!projection.hasMore || projection.messageBeforePosition === undefined)
      return;
    try {
      const page = await getWorkspaceTimeline(presentationId, {
        beforePosition: projection.messageBeforePosition,
      });
      commitProjection((current) =>
        mergeTimelinePage(current, page, { preserveEventHighWatermark: true }),
      );
      setHistoryError(null);
    } catch (loadError: unknown) {
      setHistoryError(
        publicWorkspaceErrorMessage(
          loadError,
          "이전 대화 기록을 불러오지 못했습니다.",
        ),
      );
      throw loadError;
    }
  }, [
    commitProjection,
    presentationId,
    projection.hasMore,
    projection.messageBeforePosition,
  ]);

  const rememberFailedIntent = useCallback(
    async (intent: WorkspacePendingIntent, requestError: unknown) => {
      if (isRevisionConflict(requestError)) {
        pendingIntentRef.current = intent;
        setRetryIntent({
          reason: "revision_conflict",
          command:
            intent.kind === "command" ? intent.request.command : undefined,
          action: intent.kind === "action" ? intent.request.action : undefined,
        });
        try {
          await rebase();
          setRequestError(
            "다른 탭에서 프레젠테이션이 변경되었습니다. 최신 기록을 확인한 뒤 이 요청을 다시 시도하세요.",
          );
        } catch (rebaseError: unknown) {
          setHistoryError(
            publicWorkspaceErrorMessage(
              rebaseError,
              "최신 대화 기록을 불러오지 못했습니다.",
            ),
          );
        }
        return;
      }

      if (isAmbiguousWriteFailure(requestError)) {
        pendingIntentRef.current = intent;
        setRetryIntent({
          reason: "ambiguous",
          command:
            intent.kind === "command" ? intent.request.command : undefined,
          action: intent.kind === "action" ? intent.request.action : undefined,
        });
        setRequestError(
          "요청 결과를 확인하지 못했습니다. 같은 요청 키로 안전하게 다시 확인할 수 있습니다.",
        );
        return;
      }

      pendingIntentRef.current = null;
      setRetryIntent(null);
      setRequestError(
        publicWorkspaceErrorMessage(
          requestError,
          "작업 요청을 처리하지 못했습니다.",
        ),
      );
    },
    [rebase],
  );

  const sendCommand = useCallback(
    async (command: string, options: WorkspaceCommandOptions = {}) => {
      if (!commandsEnabled || projectionRef.current.writesSafe !== true)
        throw new Error("이 작업공간은 현재 읽기 전용입니다.");
      const context = options.context;
      const request: WorkspaceCommandRequest = {
        command,
        commandType:
          options.commandType ??
          (context?.current_slide === undefined
            ? "presentation.edit"
            : "slide.edit"),
        context,
        assetIds: options.assetIds,
        idempotencyKey: idempotencyKey(),
        expectedRevision: projection.revision,
      };
      setSubmitting(true);
      setRequestError(null);
      try {
        const receipt = await postWorkspaceCommand(presentationId, request);
        commitProjection((current) =>
          mergeTimelinePage(current, receipt.timeline, {
            preservePagination: true,
          }),
        );
        pendingIntentRef.current = null;
        setRetryIntent(null);
      } catch (commandError: unknown) {
        await rememberFailedIntent({ kind: "command", request }, commandError);
        if (pendingIntentRef.current) throw commandError;
        throw commandError;
      } finally {
        setSubmitting(false);
      }
    },
    [
      commandsEnabled,
      commitProjection,
      presentationId,
      projection.revision,
      rememberFailedIntent,
    ],
  );

  const performAction = useCallback(
    async (
      operationId: string,
      action: WorkspaceOperationActionRequest["action"],
    ) => {
      if (!commandsEnabled || projectionRef.current.writesSafe !== true)
        throw new Error("이 작업공간은 현재 읽기 전용입니다.");
      setSubmitting(true);
      setRequestError(null);
      const operation = projection.items.find(
        (item) =>
          item.anchorType === "operation" && item.operationId === operationId,
      );
      const request: WorkspaceOperationActionRequest = {
        action,
        idempotencyKey: idempotencyKey(),
        expectedRevision: projection.revision,
        expectedOperationVersion:
          operation?.anchorType === "operation" ? operation.version : undefined,
      };
      try {
        const receipt = await postWorkspaceOperationAction(
          operationId,
          request,
        );
        commitProjection((current) =>
          mergeTimelinePage(current, receipt.timeline, {
            preservePagination: true,
          }),
        );
        pendingIntentRef.current = null;
        setRetryIntent(null);
      } catch (actionError: unknown) {
        await rememberFailedIntent(
          { kind: "action", operationId, request },
          actionError,
        );
        if (pendingIntentRef.current) throw actionError;
        throw actionError;
      } finally {
        setSubmitting(false);
      }
    },
    [
      commandsEnabled,
      commitProjection,
      projection.items,
      projection.revision,
      rememberFailedIntent,
    ],
  );

  const retryPendingIntent = useCallback(async () => {
    const pending = pendingIntentRef.current;
    if (
      !pending ||
      !commandsEnabled ||
      projectionRef.current.writesSafe !== true
    )
      return;

    let next: WorkspacePendingIntent = pending;
    if (retryIntent?.reason === "revision_conflict") {
      if (pending.kind === "command") {
        next = {
          ...pending,
          request: {
            ...pending.request,
            expectedRevision: projectionRef.current.revision,
          },
        };
      } else {
        const operation = projectionRef.current.items.find(
          (item) =>
            item.anchorType === "operation" &&
            item.operationId === pending.operationId,
        );
        next = {
          ...pending,
          request: {
            ...pending.request,
            expectedRevision: projectionRef.current.revision,
            expectedOperationVersion:
              operation?.anchorType === "operation"
                ? operation.version
                : pending.request.expectedOperationVersion,
          },
        };
      }
    }

    pendingIntentRef.current = next;
    setSubmitting(true);
    setRequestError(null);
    try {
      const receipt =
        next.kind === "command"
          ? await postWorkspaceCommand(presentationId, next.request)
          : await postWorkspaceOperationAction(next.operationId, next.request);
      commitProjection((current) =>
        mergeTimelinePage(current, receipt.timeline, {
          preservePagination: true,
        }),
      );
      pendingIntentRef.current = null;
      setRetryIntent(null);
    } catch (retryError: unknown) {
      await rememberFailedIntent(next, retryError);
      throw retryError;
    } finally {
      setSubmitting(false);
    }
  }, [
    commandsEnabled,
    commitProjection,
    presentationId,
    rememberFailedIntent,
    retryIntent?.reason,
  ]);

  const retryLoad = useCallback(async () => {
    setLoading(true);
    setHistoryError(null);
    setConnection("connecting");
    try {
      await rebase();
      setConnection("live");
    } catch (retryError: unknown) {
      setHistoryError(
        publicWorkspaceErrorMessage(
          retryError,
          "대화 기록 복구에 실패했습니다.",
        ),
      );
      setConnection("offline");
    } finally {
      setLoading(false);
    }
  }, [rebase]);

  return {
    projection,
    loading,
    submitting,
    historyError,
    requestError,
    retryIntent,
    connection,
    presentationRefreshStates,
    loadOlder,
    sendCommand,
    performAction,
    retryPendingIntent,
    retryLoad,
    writesEnabled: commandsEnabled && projection.writesSafe === true,
  };
}
