"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { WorkspaceComposer } from "./WorkspaceComposer";
import { WorkspaceMessage } from "./WorkspaceMessage";
import { WorkspaceOperationCard } from "./WorkspaceOperationCard";
import { useWorkspaceTimeline } from "./useWorkspace";
import type { WorkspaceCommandDispatcher } from "./types";

interface WorkspaceChatPanelProps {
  presentationId: string;
  currentSlide?: number;
  commandsEnabled?: boolean;
  onPresentationChanged?: () => void;
  onCommandDispatcherReady?: (dispatcher: WorkspaceCommandDispatcher | null) => void;
}

const CONNECTION_LABELS = {
  connecting: "연결 중",
  live: "실시간 연결",
  polling: "연결 복구 중",
  offline: "오프라인",
};

export default function WorkspaceChatPanel({
  presentationId,
  currentSlide,
  commandsEnabled = true,
  onPresentationChanged,
  onCommandDispatcherReady,
}: WorkspaceChatPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const hasInitialScrollRef = useRef(false);
  const shouldFollowLatestRef = useRef(true);
  const previousItemCountRef = useRef(0);
  const previousEventHighWatermarkRef = useRef<string | undefined>(undefined);
  const prependRestoreRef = useRef<{
    scrollHeight: number;
    scrollTop: number;
    itemCount: number;
  } | null>(null);
  const {
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
  } = useWorkspaceTimeline({
    presentationId,
    enabled: true,
    commandsEnabled,
    onPresentationChanged,
  });

  const sendCommandRef = useRef(sendCommand);
  useEffect(() => {
    sendCommandRef.current = sendCommand;
  }, [sendCommand]);
  const commandDispatcher = useCallback<WorkspaceCommandDispatcher>(
    (command, options) => sendCommandRef.current(command, options),
    [],
  );

  useEffect(() => {
    onCommandDispatcherReady?.(commandDispatcher);
    return () => onCommandDispatcherReady?.(null);
  }, [commandDispatcher, onCommandDispatcherReady]);

  useLayoutEffect(() => {
    hasInitialScrollRef.current = false;
    shouldFollowLatestRef.current = true;
    previousItemCountRef.current = 0;
    previousEventHighWatermarkRef.current = undefined;
    prependRestoreRef.current = null;
    setLoadingOlder(false);
  }, [presentationId]);

  useLayoutEffect(() => {
    if (loading) return;

    const scrollElement = scrollRef.current;
    if (!scrollElement) return;
    const eventHighWatermarkAdvanced =
      projection.eventHighWatermark !== previousEventHighWatermarkRef.current;

    const prependRestore = prependRestoreRef.current;
    if (prependRestore) {
      if (projection.items.length > prependRestore.itemCount) {
        const addedHeight = scrollElement.scrollHeight - prependRestore.scrollHeight;
        scrollElement.scrollTop = prependRestore.scrollTop + addedHeight;
        prependRestoreRef.current = null;
        shouldFollowLatestRef.current = false;
      } else if (!loadingOlder) {
        prependRestoreRef.current = null;
      }
      previousItemCountRef.current = projection.items.length;
      previousEventHighWatermarkRef.current = projection.eventHighWatermark;
      return;
    }

    if (!hasInitialScrollRef.current) {
      scrollElement.scrollTop = scrollElement.scrollHeight;
      hasInitialScrollRef.current = true;
      shouldFollowLatestRef.current = true;
    } else if (
      (projection.items.length > previousItemCountRef.current || eventHighWatermarkAdvanced) &&
      shouldFollowLatestRef.current
    ) {
      scrollElement.scrollTop = scrollElement.scrollHeight;
    }

    previousItemCountRef.current = projection.items.length;
    previousEventHighWatermarkRef.current = projection.eventHighWatermark;
  }, [loading, loadingOlder, projection.eventHighWatermark, projection.items.length]);

  const handleTimelineScroll = useCallback(() => {
    const scrollElement = scrollRef.current;
    if (!scrollElement) return;
    const distanceFromBottom =
      scrollElement.scrollHeight - scrollElement.scrollTop - scrollElement.clientHeight;
    shouldFollowLatestRef.current = distanceFromBottom <= 48;
  }, []);

  const handleLoadOlder = useCallback(async () => {
    const scrollElement = scrollRef.current;
    if (!scrollElement || loadingOlder) return;

    prependRestoreRef.current = {
      scrollHeight: scrollElement.scrollHeight,
      scrollTop: scrollElement.scrollTop,
      itemCount: projection.items.length,
    };
    setLoadingOlder(true);
    try {
      await loadOlder();
    } catch {
      prependRestoreRef.current = null;
    } finally {
      setLoadingOlder(false);
    }
  }, [loadOlder, loadingOlder, projection.items.length]);

  return (
    <section className="flex h-full w-full min-h-0 min-w-0 flex-col bg-slate-50" aria-label="프레젠테이션 작업 대화">
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Slides Workbench</h2>
          <p className="text-xs text-slate-500">생성 및 수정 내역</p>
        </div>
        <span className="text-xs text-slate-500" role="status" aria-live="polite">
          {CONNECTION_LABELS[connection]}
        </span>
      </header>

      <div
        ref={scrollRef}
        data-testid="workspace-timeline-scroll"
        className="min-h-0 flex-1 overflow-y-auto px-3 py-4"
        onScroll={handleTimelineScroll}
      >
        {projection.hasMore && (
          <div className="mb-4 text-center">
            <button
              type="button"
              onClick={() => void handleLoadOlder()}
              disabled={loadingOlder}
              className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
            >
              {loadingOlder ? "이전 기록 불러오는 중" : "이전 기록 불러오기"}
            </button>
          </div>
        )}
        {loading && <p className="py-8 text-center text-sm text-slate-500">대화 기록을 불러오는 중입니다.</p>}
        {!loading && projection.items.length === 0 && !error && (
          <p className="py-8 text-center text-sm text-slate-500">아직 기록된 대화가 없습니다.</p>
        )}
        <div className="space-y-3">
          {projection.items.map((item) => {
            if (item.anchorType === "message") return <WorkspaceMessage key={item.id} message={item} />;
            if (item.anchorType === "operation") {
              return (
                <WorkspaceOperationCard
                  key={item.id}
                  operation={item}
                  currentRevision={projection.revision}
                  disabled={submitting || !commandsEnabled}
                  onAction={(operationId, action) => void performAction(operationId, action)}
                  onReExport={(operationType) => {
                    const format = operationType === "export.pdf" ? "pdf" : "pptx";
                    void sendCommandRef.current(`${format.toUpperCase()}로 다시 내보내기`, {
                      commandType: operationType,
                      context: { format },
                    });
                  }}
                />
              );
            }
            return (
              <article key={item.id} className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm" role="alert">
                지원되지 않는 타임라인 항목입니다. 새로고침해 주세요.
              </article>
            );
          })}
        </div>
        {error && (
          <div className="mt-3 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700" role="alert">
            <p>{error}</p>
            {retryIntent?.command && (
              <blockquote className="mt-2 rounded-lg border border-red-200 bg-white/70 px-3 py-2 text-xs text-slate-700">
                {retryIntent.command}
              </blockquote>
            )}
            {retryIntent && (
              <button
                type="button"
                onClick={() => void retryPendingIntent().catch(() => undefined)}
                disabled={submitting || !commandsEnabled}
                data-testid="workspace-retry-intent"
                className="mt-2 font-semibold underline disabled:cursor-not-allowed disabled:opacity-50"
              >
                {retryIntent.reason === "revision_conflict"
                  ? "최신 버전 기준으로 다시 시도"
                  : "같은 요청으로 다시 확인"}
              </button>
            )}
            {!retryIntent && <button type="button" onClick={() => void retryLoad()} className="mt-2 font-semibold underline">
              다시 연결
            </button>}
          </div>
        )}
      </div>

      <WorkspaceComposer
        disabled={submitting || loading || connection === "offline" || !commandsEnabled}
        onSend={(command) =>
          sendCommand(command, { context: { current_slide: currentSlide } })
        }
      />
    </section>
  );
}
