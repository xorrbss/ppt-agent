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
import type {
  WorkspaceCapability,
  WorkspaceCommandOptions,
  WorkspaceCommandRequest,
  WorkspaceOperationActionRequest,
  WorkspaceProjection,
} from "./types";

export type WorkspaceCapabilityState = "disabled" | "checking" | "supported" | "unavailable";
export type WorkspaceConnectionState = "connecting" | "live" | "polling" | "offline";
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

export function useWorkspaceCapability(enabled: boolean): {
  state: WorkspaceCapabilityState;
  capability: WorkspaceCapability;
} {
  const [state, setState] = useState<WorkspaceCapabilityState>(enabled ? "checking" : "disabled");
  const [capability, setCapability] = useState(EMPTY_CAPABILITY);

  useEffect(() => {
    if (!enabled) {
      setState("disabled");
      setCapability(EMPTY_CAPABILITY);
      return;
    }
    const controller = new AbortController();
    setState("checking");
    getWorkspaceCapabilities(controller.signal)
      .then((next) => {
        if (controller.signal.aborted) return;
        setCapability(next);
        setState(next.supported && next.timeline ? "supported" : "unavailable");
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        console.warn("Workspace capability check failed; chat-first writes remain disabled.", error);
        setCapability(EMPTY_CAPABILITY);
        setState("unavailable");
      });
    return () => controller.abort();
  }, [enabled]);

  return { state, capability };
}

function idempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function isRevisionConflict(error: unknown): error is WorkspaceApiError {
  return (
    error instanceof WorkspaceApiError &&
    (error.code === "revision_conflict" ||
      (error.status === 409 && error.code === "stale_revision"))
  );
}

function isAmbiguousWriteFailure(error: unknown): boolean {
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
  onPresentationChanged?: () => void;
}

export function useWorkspaceTimeline({
  presentationId,
  enabled,
  commandsEnabled = true,
  onPresentationChanged,
}: UseWorkspaceTimelineOptions) {
  const [projection, setProjection] = useState<WorkspaceProjection>(createWorkspaceProjection);
  const [loading, setLoading] = useState(enabled);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryIntent, setRetryIntent] = useState<WorkspaceRetryIntent | null>(null);
  const [connection, setConnection] = useState<WorkspaceConnectionState>("connecting");
  const projectionRef = useRef<WorkspaceProjection>(createWorkspaceProjection());
  const cursorRef = useRef<string | undefined>(undefined);
  const completionSeededRef = useRef(false);
  const succeededRef = useRef(new Set<string>());
  const pendingIntentRef = useRef<WorkspacePendingIntent | null>(null);

  const commitProjection = useCallback(
    (update: (current: WorkspaceProjection) => WorkspaceProjection): WorkspaceProjection => {
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
      commitProjection(() => mergeTimelinePage(createWorkspaceProjection(), page));
      return page;
    },
    [commitProjection, presentationId],
  );

  useEffect(() => {
    if (!enabled || !presentationId) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setConnection("connecting");
    commitProjection(() => createWorkspaceProjection());
    completionSeededRef.current = false;
    succeededRef.current = new Set();
    pendingIntentRef.current = null;
    setRetryIntent(null);

    rebase(controller.signal)
      .then(() => setConnection("live"))
      .catch((loadError: unknown) => {
        if (controller.signal.aborted) return;
        setError(loadError instanceof Error ? loadError.message : "대화 기록을 불러오지 못했습니다.");
        setConnection("offline");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [commitProjection, enabled, presentationId, rebase]);

  useEffect(() => {
    if (!enabled || loading || error || !presentationId) return;
    const controller = new AbortController();
    let failures = 0;
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
          if (controller.signal.aborted) throw new DOMException("Aborted", "AbortError");
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
        throw new Error("대화 작업의 최신 상태를 복구하지 못했습니다.");
      })();
      const trackedRecovery = recovery.finally(() => {
        if (projectionRecovery === trackedRecovery) projectionRecovery = null;
      });
      projectionRecovery = trackedRecovery;
      return trackedRecovery;
    };

    const applyEventPage = (page: Awaited<ReturnType<typeof getWorkspaceEvents>>) => {
      const next = commitProjection((current) => applyOperationEvents(current, page));
      return next.rebaseRequired ? recoverProjection() : Promise.resolve();
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
            onEvent: (event, cursor) => {
              void applyEventPage({
                events: [event],
                eventHighWatermark: cursor,
              }).catch((recoveryError: unknown) => {
                if (controller.signal.aborted) return;
                setError(
                  recoveryError instanceof Error
                    ? recoveryError.message
                    : "대화 작업의 최신 상태를 복구하지 못했습니다.",
                );
                setConnection("offline");
                controller.abort();
              });
            },
          });
          failures = 0;
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
              failures = 0;
              continue;
            } catch (rebaseError: unknown) {
              if (controller.signal.aborted) return;
              setError(rebaseError instanceof Error ? rebaseError.message : "대화 기록 복구에 실패했습니다.");
            }
          }
          failures += 1;
          setConnection(failures >= 2 ? "polling" : "connecting");
          try {
            await wait(failures >= 2 ? 2500 : 1000, controller.signal);
          } catch {
            return;
          }
        }
      }
    };
    void run();
    return () => controller.abort();
  }, [commitProjection, enabled, error, loading, presentationId, rebase]);

  useEffect(() => {
    const succeeded = projection.items
      .filter(
        (item) =>
          item.anchorType === "operation" &&
          item.status === "succeeded" &&
          item.operationType !== "quality.review" &&
          item.operationType !== "export.pptx" &&
          item.operationType !== "export.pdf",
      )
      .map((item) => item.id);
    if (!completionSeededRef.current) {
      succeededRef.current = new Set(succeeded);
      completionSeededRef.current = true;
      return;
    }
    const hasNewCompletion = succeeded.some((id) => !succeededRef.current.has(id));
    succeeded.forEach((id) => succeededRef.current.add(id));
    if (hasNewCompletion) onPresentationChanged?.();
  }, [onPresentationChanged, projection.items]);

  const loadOlder = useCallback(async () => {
    if (!projection.hasMore || projection.messageBeforePosition === undefined) return;
    const page = await getWorkspaceTimeline(presentationId, {
      beforePosition: projection.messageBeforePosition,
    });
    commitProjection((current) => mergeTimelinePage(current, page));
  }, [commitProjection, presentationId, projection.hasMore, projection.messageBeforePosition]);

  const rememberFailedIntent = useCallback(
    async (intent: WorkspacePendingIntent, requestError: unknown) => {
      if (isRevisionConflict(requestError)) {
        pendingIntentRef.current = intent;
        setRetryIntent({
          reason: "revision_conflict",
          command: intent.kind === "command" ? intent.request.command : undefined,
          action: intent.kind === "action" ? intent.request.action : undefined,
        });
        try {
          await rebase();
          setError(
            "다른 탭에서 프레젠테이션이 변경되었습니다. 최신 기록을 확인한 뒤 이 요청을 다시 시도하세요.",
          );
        } catch (rebaseError: unknown) {
          setError(
            rebaseError instanceof Error
              ? rebaseError.message
              : "최신 대화 기록을 불러오지 못했습니다.",
          );
        }
        return;
      }

      if (isAmbiguousWriteFailure(requestError)) {
        pendingIntentRef.current = intent;
        setRetryIntent({
          reason: "ambiguous",
          command: intent.kind === "command" ? intent.request.command : undefined,
          action: intent.kind === "action" ? intent.request.action : undefined,
        });
        setError(
          "요청 결과를 확인하지 못했습니다. 같은 요청 키로 안전하게 다시 확인할 수 있습니다.",
        );
        return;
      }

      pendingIntentRef.current = null;
      setRetryIntent(null);
      setError(
        requestError instanceof Error
          ? requestError.message
          : "작업 요청을 처리하지 못했습니다.",
      );
    },
    [rebase],
  );

  const sendCommand = useCallback(
    async (command: string, options: WorkspaceCommandOptions = {}) => {
      const context = options.context;
      const request: WorkspaceCommandRequest = {
        command,
        commandType:
          options.commandType ??
          (context?.current_slide === undefined ? "presentation.edit" : "slide.edit"),
        context,
        idempotencyKey: idempotencyKey(),
        expectedRevision: projection.revision,
      };
      if (!commandsEnabled) throw new Error("이 작업공간은 현재 읽기 전용입니다.");
      setSubmitting(true);
      setError(null);
      try {
        const page = await postWorkspaceCommand(presentationId, request);
        commitProjection((current) =>
          mergeTimelinePage(current, page, { preservePagination: true }),
        );
        pendingIntentRef.current = null;
        setRetryIntent(null);
      } catch (commandError: unknown) {
        await rememberFailedIntent({ kind: "command", request }, commandError);
        if (pendingIntentRef.current) throw commandError;
        setError(commandError instanceof Error ? commandError.message : "명령을 전송하지 못했습니다.");
        throw commandError;
      } finally {
        setSubmitting(false);
      }
    },
    [commandsEnabled, commitProjection, presentationId, projection.revision, rememberFailedIntent],
  );

  const performAction = useCallback(
    async (operationId: string, action: WorkspaceOperationActionRequest["action"]) => {
      if (!commandsEnabled) throw new Error("이 작업공간은 현재 읽기 전용입니다.");
      setSubmitting(true);
      setError(null);
      const operation = projection.items.find(
        (item) => item.anchorType === "operation" && item.operationId === operationId,
      );
      const request: WorkspaceOperationActionRequest = {
        action,
        idempotencyKey: idempotencyKey(),
        expectedRevision: projection.revision,
        expectedOperationVersion:
          operation?.anchorType === "operation" ? operation.version : undefined,
      };
      try {
        const page = await postWorkspaceOperationAction(operationId, request);
        commitProjection((current) =>
          mergeTimelinePage(current, page, { preservePagination: true }),
        );
        pendingIntentRef.current = null;
        setRetryIntent(null);
      } catch (actionError: unknown) {
        await rememberFailedIntent(
          { kind: "action", operationId, request },
          actionError,
        );
        if (pendingIntentRef.current) throw actionError;
        setError(actionError instanceof Error ? actionError.message : "작업 요청을 처리하지 못했습니다.");
        throw actionError;
      } finally {
        setSubmitting(false);
      }
    },
    [commandsEnabled, commitProjection, projection.items, projection.revision, rememberFailedIntent],
  );

  const retryPendingIntent = useCallback(async () => {
    const pending = pendingIntentRef.current;
    if (!pending || !commandsEnabled) return;

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
    setError(null);
    try {
      const page =
        next.kind === "command"
          ? await postWorkspaceCommand(presentationId, next.request)
          : await postWorkspaceOperationAction(next.operationId, next.request);
      commitProjection((current) =>
        mergeTimelinePage(current, page, { preservePagination: true }),
      );
      pendingIntentRef.current = null;
      setRetryIntent(null);
    } catch (retryError: unknown) {
      await rememberFailedIntent(next, retryError);
      throw retryError;
    } finally {
      setSubmitting(false);
    }
  }, [commandsEnabled, commitProjection, presentationId, rememberFailedIntent, retryIntent?.reason]);

  const retryLoad = useCallback(async () => {
    setLoading(true);
    setError(null);
    setConnection("connecting");
    try {
      await rebase();
      setConnection("live");
    } catch (retryError: unknown) {
      setError(retryError instanceof Error ? retryError.message : "대화 기록 복구에 실패했습니다.");
      setConnection("offline");
    } finally {
      setLoading(false);
    }
  }, [rebase]);

  return {
    projection,
    loading,
    submitting,
    error,
    retryIntent,
    connection,
    loadOlder,
    sendCommand,
    performAction,
    retryPendingIntent,
    retryLoad,
  };
}
