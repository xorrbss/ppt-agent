"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { notify } from "@/components/ui/sonner";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import type { WorkspaceCommandDispatcher } from "../workspace";
import { PageRemakePreviewImage } from "./PageRemakePreviewImage";
import {
  describePageRemakeFailure,
  summarizePageRemakeIssues,
  PAGE_REMAKE_PUBLISH_FAILURE_FALLBACK,
  type PageRemakeIssue,
} from "./authoredPageRemakePreview";

export type PageRemakeHistoryEntry = {
  preview_task_id: string;
  slide_index: number;
  status: string;
  phase?: string | null;
  candidate_image?: string | null;
  candidate_issues?: PageRemakeIssue[];
  validation?: { candidate_dom?: string; candidate_change?: string } | null;
  restorable: boolean;
  error_code?: string | null;
  created_at?: string;
  updated_at?: string;
};

/**
 * The remake flow lives outside the workspace conversation, so a reload used to
 * drop everything the user had generated. The backend keeps each candidate, so
 * the timeline replays it here and lets a still-valid preview be applied.
 */
export function PageRemakeHistory({
  presentationId,
  entries,
  disabled = false,
  onWorkspaceCommand,
  onApplied,
  onRegenerate,
}: {
  presentationId: string;
  entries: PageRemakeHistoryEntry[];
  disabled?: boolean;
  /**
   * Chat-first hosts must publish through the workspace command pipeline; the
   * legacy REST route is fenced off for them and answers 409.
   */
  onWorkspaceCommand?: WorkspaceCommandDispatcher;
  onApplied?: (slideIndex: number) => void;
  onRegenerate?: (slideIndex: number) => void;
}) {
  const [publishingId, setPublishingId] = useState<string | null>(null);
  const [appliedIds, setAppliedIds] = useState<string[]>([]);

  if (!entries.length) return null;

  return (
    <div className="space-y-3" data-testid="page-remake-history">
      {entries.map((entry) => (
        <PageRemakeHistoryCard
          key={entry.preview_task_id}
          presentationId={presentationId}
          entry={entry}
          disabled={disabled}
          onWorkspaceCommand={onWorkspaceCommand}
          applied={appliedIds.includes(entry.preview_task_id)}
          publishing={publishingId === entry.preview_task_id}
          onPublishingChange={setPublishingId}
          onApplied={(taskId) => {
            setAppliedIds((previous) => [...previous, taskId]);
            onApplied?.(entry.slide_index);
          }}
          onRegenerate={onRegenerate}
        />
      ))}
    </div>
  );
}

function PageRemakeHistoryCard({
  presentationId,
  entry,
  disabled,
  applied,
  publishing,
  onWorkspaceCommand,
  onPublishingChange,
  onApplied,
  onRegenerate,
}: {
  presentationId: string;
  entry: PageRemakeHistoryEntry;
  disabled: boolean;
  applied: boolean;
  publishing: boolean;
  onWorkspaceCommand?: WorkspaceCommandDispatcher;
  onPublishingChange: (taskId: string | null) => void;
  onApplied: (taskId: string) => void;
  onRegenerate?: (slideIndex: number) => void;
}) {
  const pageNumber = entry.slide_index + 1;
  const warned = entry.validation?.candidate_dom === "warning";
  const issueGroups = warned
    ? summarizePageRemakeIssues(entry.candidate_issues)
    : [];
  const publishedAlready = entry.phase === "published" || applied;
  const failed = entry.status === "error";
  const canApply = entry.restorable && !applied && !disabled;

  const publish = useCallback(async () => {
    if (!canApply || publishing) return;
    onPublishingChange(entry.preview_task_id);
    try {
      if (onWorkspaceCommand) {
        await onWorkspaceCommand(`${pageNumber}페이지 미리보기를 적용해 줘`, {
          commandType: "slide.regenerate",
          context: {
            slide_index: entry.slide_index,
            preview_task_id: entry.preview_task_id,
            source: "page_remake_history",
          },
        });
        notify.success("페이지 적용 작업을 대화에 추가했습니다.");
      } else {
        await PresentationGenerationApi.publishAuthoredPageRemake(
          presentationId,
          entry.slide_index,
          entry.preview_task_id,
        );
        notify.success(`${pageNumber}페이지의 새 버전을 적용했습니다.`);
      }
      onApplied(entry.preview_task_id);
    } catch (error) {
      const described = describePageRemakeFailure(
        error,
        PAGE_REMAKE_PUBLISH_FAILURE_FALLBACK,
      );
      notify.error("페이지 적용 실패", described.message);
    } finally {
      onPublishingChange(null);
    }
  }, [
    canApply,
    entry,
    onApplied,
    onPublishingChange,
    onWorkspaceCommand,
    pageNumber,
    presentationId,
    publishing,
  ]);

  return (
    <article
      className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"
      data-testid={`page-remake-history-${entry.slide_index}`}
      aria-label={`${pageNumber}페이지 다시 만들기 기록`}
    >
      <header className="mb-3 flex items-center gap-2">
        <RefreshCw className="h-4 w-4 shrink-0 text-indigo-500" />
        <h3 className="text-sm font-semibold text-slate-900">
          페이지 다시 만들기 · {pageNumber}페이지
        </h3>
      </header>

      {failed ? (
        <p
          className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-950"
          data-testid="page-remake-history-failed"
        >
          {
            describePageRemakeFailure(
              entry.error_code,
              "새 페이지를 만들지 못했습니다.",
            ).message
          }
        </p>
      ) : (
        <>
          {entry.candidate_image && (
            <PageRemakePreviewImage
              image={entry.candidate_image}
              pageNumber={pageNumber}
            />
          )}

          <div className="mt-3 space-y-2 text-sm">
            {warned ? (
              <div
                className="flex items-start gap-2 text-amber-900"
                data-testid="page-remake-history-warning"
              >
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <div className="space-y-1">
                  <p>
                    자동 검사에서 확인할 항목이 발견되었습니다
                    {issueGroups.length > 0 &&
                      ` (${issueGroups
                        .map((group) => `${group.label} ${group.count}건`)
                        .join(", ")})`}
                    .
                  </p>
                  {/* The counts alone prompted "what is this?", so explain them. */}
                  {issueGroups.map((group) => (
                    <p
                      key={group.category}
                      className="text-xs leading-5 text-amber-800"
                      data-testid={`page-remake-issue-help-${group.category}`}
                    >
                      <span className="font-medium">{group.label}</span> —{" "}
                      {group.description}
                    </p>
                  ))}
                </div>
              </div>
            ) : (
              <p className="flex items-start gap-2 text-emerald-900">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                <span>자동 검사를 통과했습니다.</span>
              </p>
            )}

            {publishedAlready ? (
              <p
                className="text-slate-600"
                data-testid="page-remake-history-applied"
              >
                이 버전을 적용했습니다.
              </p>
            ) : entry.restorable ? (
              <p className="text-slate-600">
                원본은 아직 변경되지 않았습니다. 지금 적용할 수 있습니다.
              </p>
            ) : (
              <p
                className="text-slate-500"
                data-testid="page-remake-history-stale"
              >
                이후 페이지가 변경되어 이 미리보기는 적용할 수 없습니다. 다시
                만들어 주세요.
              </p>
            )}
          </div>
        </>
      )}

      <div className="mt-3 flex justify-end gap-2">
        {onRegenerate && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={disabled || publishing}
            onClick={() => onRegenerate(entry.slide_index)}
            data-testid={`page-remake-history-regenerate-${entry.slide_index}`}
          >
            <RefreshCw className="h-4 w-4" />
            다시 만들기
          </Button>
        )}
        {canApply && (
          <Button
            type="button"
            size="sm"
            disabled={publishing}
            onClick={publish}
            data-testid={`page-remake-history-apply-${entry.slide_index}`}
          >
            {publishing && <Loader2 className="h-4 w-4 animate-spin" />}
            {publishing ? "적용 중" : "이 버전 적용"}
          </Button>
        )}
      </div>
    </article>
  );
}

/** Loads remake history once per presentation. */
export function usePageRemakeHistory(
  presentationId: string,
  enabled: boolean,
  reloadToken: number,
) {
  const [entries, setEntries] = useState<PageRemakeHistoryEntry[]>([]);

  useEffect(() => {
    if (!enabled || !presentationId) return;
    let cancelled = false;
    (async () => {
      try {
        const history =
          await PresentationGenerationApi.listAuthoredPageRemakePreviews(
            presentationId,
          );
        if (!cancelled && Array.isArray(history)) setEntries(history);
      } catch {
        // History is additive context; a failure must not break the timeline.
        if (!cancelled) setEntries([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [presentationId, enabled, reloadToken]);

  return entries;
}
