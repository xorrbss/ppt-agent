"use client";

import { useContext, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Info,
  Loader2,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { notify } from "@/components/ui/sonner";
import type { RootState } from "@/store/store";
import { ReactReduxContext } from "react-redux";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { PageRemakePreviewImage } from "./PageRemakePreviewImage";
import type { WorkspaceCommandDispatcher } from "../workspace";
import {
  describePageRemakeFailure,
  hasQualityWarning,
  isUnchangedCandidate,
  pageRemakeTelemetryNote,
  summarizePageRemakeIssues,
  PAGE_REMAKE_PREVIEW_FAILURE_FALLBACK,
  PAGE_REMAKE_PUBLISH_FAILURE_FALLBACK,
  PAGE_REMAKE_UNCHANGED_BODY,
  PAGE_REMAKE_UNCHANGED_HEADLINE,
  type PageRemakeFailure,
  type PageRemakePreview,
} from "./authoredPageRemakePreview";
import {
  masterPinnedBodyOnlyContext,
  presentationMasterPin,
  presentationSlideLayoutId,
} from "../utils/masterPresentation";

const POLL_INTERVAL_MS = 2_000;
const PREVIEW_TIMEOUT_MS = 15 * 60 * 1_000;

function PageRemakeFlow({
  presentationId,
  currentSlide,
  disabled = false,
  onWorkspaceCommand,
  onReload,
  onDismiss,
}: {
  presentationId: string;
  currentSlide: number;
  disabled?: boolean;
  onWorkspaceCommand?: WorkspaceCommandDispatcher;
  onReload?: () => void;
  onDismiss: () => void;
}) {
  const reduxContext = useContext(ReactReduxContext);
  const [previewTaskId, setPreviewTaskId] = useState<string | null>(null);
  const [preview, setPreview] = useState<PageRemakePreview | null>(null);
  const [statusMessage, setStatusMessage] = useState("");
  const [failure, setFailure] = useState<PageRemakeFailure | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isPublishing, setIsPublishing] = useState(false);
  // Every start claims a new run id; unmount, cancel and regenerate invalidate
  // the previous one so an in-flight polling loop stops touching state.
  const runIdRef = useRef(0);
  const pageNumber = currentSlide + 1;
  const qualityWarning = preview ? hasQualityWarning(preview) : false;
  const unchangedCandidate = preview ? isUnchangedCandidate(preview) : false;
  const issueGroups = qualityWarning
    ? summarizePageRemakeIssues(preview?.candidate_issues)
    : [];

  useEffect(() => () => {
    runIdRef.current += 1;
  }, []);

  const createPreview = async () => {
    if (isGenerating || isPublishing || disabled) return;
    const runId = ++runIdRef.current;
    setIsGenerating(true);
    setPreview(null);
    setPreviewTaskId(null);
    setFailure(null);
    setStatusMessage(`${pageNumber}페이지의 새 버전을 만들고 있습니다.`);
    let failureCode: string | undefined;
    try {
      const started = await PresentationGenerationApi.previewAuthoredPageRemake(
        presentationId,
        currentSlide,
      );
      if (runIdRef.current !== runId) return;
      const taskId = started?.id;
      if (!taskId) throw new Error("페이지 다시 만들기 작업을 시작하지 못했습니다.");
      setPreviewTaskId(taskId);

      const deadline = Date.now() + PREVIEW_TIMEOUT_MS;
      while (Date.now() < deadline) {
        await new Promise((resolve) =>
          window.setTimeout(resolve, POLL_INTERVAL_MS),
        );
        if (runIdRef.current !== runId) return;
        const task = await PresentationGenerationApi.getGenerationStatus(taskId);
        if (runIdRef.current !== runId) return;
        if (task?.message) setStatusMessage(task.message);
        if (task?.status === "completed") {
          const data = task.data as PageRemakePreview | undefined;
          if (
            !data ||
            data.kind !== "authored_page_remake_preview" ||
            data.slide_index !== currentSlide ||
            data.phase !== "preview_ready"
          ) {
            throw new Error(
              "페이지 미리보기 결과가 현재 페이지와 일치하지 않습니다.",
            );
          }
          setPreview(data);
          if (hasQualityWarning(data)) {
            notify.warning(
              "새 페이지를 만들었습니다",
              "자동 검사에서 확인할 항목이 발견되었습니다. 미리보기를 확인해 주세요.",
            );
          } else if (isUnchangedCandidate(data)) {
            notify.success(
              PAGE_REMAKE_UNCHANGED_HEADLINE,
              "변경할 부분을 찾지 못했습니다. 다시 만들 수 있습니다.",
            );
          } else {
            notify.success("새 페이지 미리보기가 준비되었습니다.");
          }
          return;
        }
        if (task?.status === "error") {
          const described = describePageRemakeFailure(
            task?.error?.detail,
            PAGE_REMAKE_PREVIEW_FAILURE_FALLBACK,
          );
          failureCode = described.code;
          throw new Error(described.message);
        }
      }
      throw new Error(
        "페이지 생성 시간이 초과되었습니다. 다시 시도해 주세요.",
      );
    } catch (error) {
      if (runIdRef.current !== runId) return;
      const described = describePageRemakeFailure(
        error,
        PAGE_REMAKE_PREVIEW_FAILURE_FALLBACK,
      );
      const nextFailure = {
        message: described.message,
        code: described.code ?? failureCode,
      };
      notify.error("페이지 다시 만들기 실패", nextFailure.message);
      setFailure(nextFailure);
      setStatusMessage("");
    } finally {
      if (runIdRef.current === runId) setIsGenerating(false);
    }
  };

  const dismissFlow = () => {
    runIdRef.current += 1;
    onDismiss();
  };

  const publishPreview = async () => {
    if (!preview || !previewTaskId || isPublishing || isGenerating || disabled) {
      return;
    }
    const runId = ++runIdRef.current;
    setIsPublishing(true);
    setFailure(null);
    setStatusMessage(`${pageNumber}페이지의 새 버전을 적용하고 있습니다.`);
    try {
      if (onWorkspaceCommand) {
        const presentationData = reduxContext
          ? (reduxContext.store.getState() as RootState).presentationGeneration
              .presentationData
          : null;
        const pin = presentationMasterPin(presentationData);
        const layoutId = pin
          ? presentationSlideLayoutId(presentationData, currentSlide)
          : null;
        const pinnedContext = pin
          ? masterPinnedBodyOnlyContext(pin, layoutId)
          : null;
        if (pin && !pinnedContext) {
          throw new Error(
            "현재 페이지의 마스터 레이아웃을 확인할 수 없어 적용하지 않았습니다.",
          );
        }
        await onWorkspaceCommand(`${pageNumber}페이지 미리보기를 적용해 줘`, {
          commandType: "slide.regenerate",
          context: {
            slide_index: currentSlide,
            preview_task_id: previewTaskId,
            source: "page_remake_button",
            ...(pinnedContext ?? {}),
          },
        });
        notify.success("페이지 적용 작업을 대화에 추가했습니다.");
      } else {
        await PresentationGenerationApi.publishAuthoredPageRemake(
          presentationId,
          currentSlide,
          previewTaskId,
        );
        onReload?.();
        notify.success(`${pageNumber}페이지의 새 버전을 적용했습니다.`);
      }
      onDismiss();
    } catch (error) {
      if (runIdRef.current !== runId) return;
      const described = describePageRemakeFailure(
        error,
        PAGE_REMAKE_PUBLISH_FAILURE_FALLBACK,
      );
      notify.error("페이지 적용 실패", described.message);
      setFailure(described);
      setStatusMessage("");
    } finally {
      if (runIdRef.current === runId) setIsPublishing(false);
    }
  };

  return (
    <>
      {!preview ? (
        <section className="space-y-3 rounded-2xl border border-slate-200 bg-slate-50 p-5">
          <div className="flex items-start gap-3">
            <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600" />
            <div className="space-y-1 text-sm text-slate-700">
              <p className="font-semibold text-slate-950">현재 페이지 전용 작업</p>
              <p>내용과 의미는 유지하고 배치와 시각 구조를 새로 구성합니다.</p>
              <p>
                생성 결과는 별도 미리보기로 검증한 뒤 사용자가 승인해야
                적용됩니다.
              </p>
            </div>
          </div>
          {statusMessage && (
            <p data-testid="page-remake-status" className="text-sm text-slate-600">
              {statusMessage}
            </p>
          )}
        </section>
      ) : (
        <div className="space-y-4" data-testid="page-remake-preview">
          <PageRemakePreviewImage
            image={preview.candidate_image}
            pageNumber={pageNumber}
          />
          {qualityWarning ? (
            <div
              className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950"
              data-testid="page-remake-warning"
              role="status"
            >
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
              <div className="space-y-2">
                <p className="font-semibold">확인이 필요한 항목이 있습니다</p>
                <p>
                  새 페이지는 생성되었지만 자동 검사에서 확인할 항목이
                  발견되었습니다.
                </p>
                {issueGroups.length > 0 ? (
                  <ul
                    className="list-disc space-y-1 pl-5"
                    data-testid="page-remake-warning-issues"
                  >
                    {issueGroups.map((group) => (
                      <li key={group.category}>
                        <span>
                          {group.label} {group.count}건
                          {group.severityLabel
                            ? ` · ${group.severityLabel}`
                            : ""}
                        </span>
                        {/* A count alone does not say whether it matters. */}
                        <span
                          className="block text-xs leading-5 text-amber-800"
                          data-testid={`page-remake-issue-help-${group.category}`}
                        >
                          {group.description}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p data-testid="page-remake-warning-issues">
                    {pageRemakeTelemetryNote(
                      preview.validation?.candidate_dom_telemetry,
                    )}
                  </p>
                )}
                <p>
                  원본 페이지는 아직 변경되지 않았습니다. 미리보기를 확인한 뒤
                  이 버전을 적용하거나 다시 만들 수 있습니다.
                </p>
              </div>
            </div>
          ) : unchangedCandidate ? (
            <div
              className="flex items-start gap-3 rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-900"
              data-testid="page-remake-unchanged"
            >
              <Info className="mt-0.5 h-5 w-5 shrink-0 text-slate-500" />
              <div>
                <p className="font-semibold">
                  {PAGE_REMAKE_UNCHANGED_HEADLINE}
                </p>
                <p>{PAGE_REMAKE_UNCHANGED_BODY}</p>
              </div>
            </div>
          ) : (
            <div className="flex items-start gap-3 rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-950">
              <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0" />
              <div>
                <p className="font-semibold">미리보기 검증 완료</p>
                <p>
                  렌더링과 DOM 안전 검사를 통과했습니다. 원본은 아직 변경되지
                  않았습니다.
                </p>
              </div>
            </div>
          )}
        </div>
      )}

      {failure && (
        <div
          className="mt-4 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-950"
          data-testid="page-remake-error"
          role="alert"
        >
          <p className="font-semibold">{failure.message}</p>
          {failure.code && (
            <p
              className="mt-1 text-xs text-rose-700"
              data-testid="page-remake-error-code"
            >
              오류 코드: {failure.code}
            </p>
          )}
        </div>
      )}

      <div className="mt-4 flex justify-end gap-2">
        <Button
          type="button"
          variant="ghost"
          disabled={isGenerating || isPublishing}
          onClick={dismissFlow}
        >
          취소
        </Button>
        {!preview ? (
          <Button
            type="button"
            disabled={isGenerating || isPublishing || disabled}
            onClick={createPreview}
            data-testid="page-remake-preview-create"
          >
            {isGenerating && <Loader2 className="h-4 w-4 animate-spin" />}
            {isGenerating ? "미리보기 만드는 중" : "미리보기 만들기"}
          </Button>
        ) : (
          <>
            {(qualityWarning || unchangedCandidate) && (
              <Button
                type="button"
                variant="outline"
                disabled={isGenerating || isPublishing || disabled}
                onClick={createPreview}
                data-testid="page-remake-regenerate"
              >
                <RefreshCw className="h-4 w-4" />
                다시 만들기
              </Button>
            )}
            <Button
              type="button"
              disabled={isGenerating || isPublishing || disabled}
              onClick={publishPreview}
              data-testid="page-remake-publish"
            >
              {isPublishing && <Loader2 className="h-4 w-4 animate-spin" />}
              {isPublishing ? "적용 중" : "이 버전 적용"}
            </Button>
          </>
        )}
      </div>
    </>
  );
}

export function WorkspacePageRemakePrompt({
  presentationId,
  currentSlide,
  disabled = false,
  onWorkspaceCommand,
  onDismiss,
}: {
  presentationId: string;
  currentSlide: number;
  disabled?: boolean;
  onWorkspaceCommand: WorkspaceCommandDispatcher;
  onDismiss: () => void;
}) {
  return (
    <article
      className="rounded-2xl border border-indigo-200 bg-white p-4 shadow-sm"
      data-testid="page-remake-workspace-prompt"
      aria-label="페이지 다시 만들기"
      aria-live="polite"
    >
      <div className="mb-4 flex items-start gap-3">
        <span
          className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-indigo-50 text-sm text-indigo-600"
          aria-hidden="true"
        >
          ✦
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-slate-900">
            페이지 다시 만들기
          </h3>
          <p className="mt-1 text-xs leading-5 text-slate-500">
            {currentSlide + 1}페이지만 새 레이아웃으로 만듭니다. 미리보기 승인
            전에는 원본을 변경하지 않습니다.
          </p>
        </div>
      </div>
      <PageRemakeFlow
        presentationId={presentationId}
        currentSlide={currentSlide}
        disabled={disabled}
        onWorkspaceCommand={onWorkspaceCommand}
        onDismiss={onDismiss}
      />
    </article>
  );
}

export default function AuthoredPageRemake({
  currentSlide,
  disabled = false,
  workspaceCommandsEnabled = false,
  onWorkspacePageRemakeRequest,
}: {
  currentSlide?: number;
  disabled?: boolean;
  workspaceCommandsEnabled?: boolean;
  onWorkspacePageRemakeRequest?: (slideIndex: number) => void;
}) {
  const validSlide =
    typeof currentSlide === "number" &&
    Number.isInteger(currentSlide) &&
    currentSlide >= 0;

  return (
    <Button
      type="button"
      variant="toolbar"
      size="toolbar"
      disabled={
        disabled ||
        !validSlide ||
        !workspaceCommandsEnabled ||
        !onWorkspacePageRemakeRequest
      }
      data-testid="page-remake-trigger"
      onClick={() => {
        if (validSlide) onWorkspacePageRemakeRequest?.(currentSlide);
      }}
    >
      <RefreshCw className="h-4 w-4" />
      페이지 다시 만들기
    </Button>
  );
}
